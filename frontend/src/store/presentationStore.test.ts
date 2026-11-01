/**
 * VIZ-106 presentation mode's store: opt-in, persisted in this browser, and
 * the `<html>` attribute written when the module is IMPORTED — before React
 * renders anything — so a reload in presentation mode never paints one frame
 * at desk size first.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const KEY = 'testlookup-presentation'
const attr = () => document.documentElement.getAttribute('data-presentation')

/** A fresh copy of the module, as a page load gets it. */
async function load() {
  vi.resetModules()
  return import('./presentationStore')
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-presentation')
})
afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-presentation')
})

describe('presentationStore', () => {
  it('is off by default: no attribute, nothing on <html> that was not there before', async () => {
    const { usePresentationStore } = await load()
    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })

  it('a stored "on" sets the attribute at import time, before any render', async () => {
    localStorage.setItem(KEY, JSON.stringify({ state: { enabled: true }, version: 0 }))
    const { usePresentationStore } = await load()
    // Nothing has rendered: no component, no effect. The attribute is already there.
    expect(attr()).toBe('on')
    expect(usePresentationStore.getState().enabled).toBe(true)
  })

  it('setEnabled writes the attribute and the preference; off removes the attribute', async () => {
    const { usePresentationStore } = await load()
    usePresentationStore.getState().setEnabled(true)
    expect(attr()).toBe('on')
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toMatchObject({ state: { enabled: true } })

    usePresentationStore.getState().setEnabled(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
    expect(JSON.parse(localStorage.getItem(KEY) ?? '{}')).toMatchObject({ state: { enabled: false } })
  })

  it('survives a reload: the next page load reads it back', async () => {
    const first = await load()
    first.usePresentationStore.getState().setEnabled(true)
    document.documentElement.removeAttribute('data-presentation') // a new document
    const second = await load()
    expect(second.usePresentationStore.getState().enabled).toBe(true)
    expect(attr()).toBe('on')
  })

  it.each([
    ['a string', { state: { enabled: 'yes' } }],
    ['a number', { state: { enabled: 1 } }],
    ['no state', {}],
  ])('anything but a stored true is off (%s)', async (_what, stored) => {
    localStorage.setItem(KEY, JSON.stringify(stored))
    const { usePresentationStore } = await load()
    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })

  it('unparseable storage is off, and the module still loads', async () => {
    localStorage.setItem(KEY, '{not json')
    const { usePresentationStore } = await load()
    expect(usePresentationStore.getState().enabled).toBe(false)
  })

  it('writes nothing, and does not throw, where there is no document (a server render or a worker)', async () => {
    const { applyPresentation } = await load()
    vi.stubGlobal('document', undefined)
    try {
      expect(() => applyPresentation(true)).not.toThrow()
    } finally {
      vi.unstubAllGlobals()
    }
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })
})

/**
 * HYG-08 — presentation mode follows another tab of this browser profile. The
 * browser fires `storage` only in the OTHER documents of the origin, after the
 * other tab has written localStorage, so each case writes "the other tab's"
 * value first and then dispatches the event this document would receive.
 */
describe('presentationStore — another tab turns the mode on or off (HYG-08)', () => {
  const stored = (enabled: boolean) => JSON.stringify({ state: { enabled }, version: 0 })

  /** The other tab's write, then the event this tab receives. */
  function otherTabWrites(value: string | null, key: string | null = KEY) {
    if (value === null) localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, value)
    window.dispatchEvent(new StorageEvent('storage', { key, newValue: value, storageArea: localStorage }))
  }

  /** The sync resolves the rehydrate through a promise: let it settle. */
  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0))

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('on in the other tab: the store and the <html> attribute follow', async () => {
    const { usePresentationStore } = await load()
    expect(usePresentationStore.getState().enabled).toBe(false)

    otherTabWrites(stored(true))
    await settle()

    expect(usePresentationStore.getState().enabled).toBe(true)
    expect(attr()).toBe('on')
  })

  it('then off in the other tab: the store is off and the attribute is removed', async () => {
    const { usePresentationStore } = await load()
    otherTabWrites(stored(true))
    await settle()
    expect(attr()).toBe('on')

    otherTabWrites(stored(false))
    await settle()

    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })

  it('a localStorage.clear() in the other tab (key === null) turns the mode off', async () => {
    localStorage.setItem(KEY, stored(true))
    const { usePresentationStore } = await load()
    expect(attr()).toBe('on')

    localStorage.clear()
    window.dispatchEvent(new StorageEvent('storage', { key: null, newValue: null, storageArea: localStorage }))
    await settle()

    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })

  it('another key changing (the theme) reads nothing back and changes nothing', async () => {
    const { usePresentationStore } = await load()
    const rehydrate = vi.spyOn(usePresentationStore.persist, 'rehydrate')
    localStorage.setItem(KEY, stored(true)) // present, but not what the event is about

    otherTabWrites('{"state":{"theme":"dark"},"version":0}', 'testlookup-theme')
    await settle()

    expect(rehydrate).not.toHaveBeenCalled()
    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })

  it('a second evaluation of the module replaces the first listener: one live listener per window', async () => {
    const first = await load()
    const removed = vi.spyOn(window, 'removeEventListener')
    const added = vi.spyOn(window, 'addEventListener')

    const second = await load()

    expect(removed).toHaveBeenCalledWith('storage', first.syncPresentationFromStorage)
    expect(added).toHaveBeenCalledWith('storage', second.syncPresentationFromStorage)
    expect(added.mock.calls.filter(([type]) => type === 'storage')).toHaveLength(1)

    const firstRehydrate = vi.spyOn(first.usePresentationStore.persist, 'rehydrate')
    const secondRehydrate = vi.spyOn(second.usePresentationStore.persist, 'rehydrate')
    otherTabWrites(stored(true))
    await settle()

    expect(firstRehydrate).not.toHaveBeenCalled()
    expect(secondRehydrate).toHaveBeenCalledTimes(1)
    expect(attr()).toBe('on')
  })

  it('the sync never writes storage, so two tabs cannot echo each other', async () => {
    const { usePresentationStore } = await load()
    localStorage.setItem(KEY, stored(true))
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: stored(true), storageArea: localStorage }))
    await settle()

    expect(usePresentationStore.getState().enabled).toBe(true)
    expect(setItem).not.toHaveBeenCalled()
  })

  it('an event from another storage area (sessionStorage) is ignored', async () => {
    const { usePresentationStore, syncPresentationFromStorage } = await load()
    const rehydrate = vi.spyOn(usePresentationStore.persist, 'rehydrate')
    localStorage.setItem(KEY, stored(true))

    syncPresentationFromStorage(new StorageEvent('storage', { key: KEY, newValue: stored(true), storageArea: sessionStorage }))
    await settle()

    expect(rehydrate).not.toHaveBeenCalled()
    expect(usePresentationStore.getState().enabled).toBe(false)
    expect(document.documentElement.hasAttribute('data-presentation')).toBe(false)
  })
})
