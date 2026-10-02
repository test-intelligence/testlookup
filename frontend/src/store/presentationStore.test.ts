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
