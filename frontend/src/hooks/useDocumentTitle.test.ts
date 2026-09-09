/**
 * Tests for useDocumentTitle.
 *
 * Regression guard for the per-route browser-tab title: before this, every page
 * shared the single static <title> from index.html, so open tabs, bookmarks and
 * history entries were indistinguishable. Covers the pure formatter (brand
 * suffix, blank/whitespace collapse) and the hook's DOM effect (sets on mount,
 * updates on title change, and does NOT restore the previous title on unmount —
 * a restore would flash the base title between every navigation).
 */
import { act, renderHook } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { createElement, type PropsWithChildren } from 'react'
import { describe, expect, it } from 'vitest'
import {
  BASE_DOCUMENT_TITLE,
  formatDocumentTitle,
  routeDocumentTitle,
  useDocumentTitle,
  useRouteDocumentTitle,
} from './useDocumentTitle'

describe('formatDocumentTitle', () => {
  it('appends the brand suffix to a page title', () => {
    expect(formatDocumentTitle('Release Gate')).toBe('Release Gate · TestLookup')
  })

  it('trims surrounding whitespace before composing', () => {
    expect(formatDocumentTitle('  Failures  ')).toBe('Failures · TestLookup')
  })

  it('collapses an empty or whitespace-only title to the base title', () => {
    expect(formatDocumentTitle('')).toBe(BASE_DOCUMENT_TITLE)
    expect(formatDocumentTitle('   ')).toBe(BASE_DOCUMENT_TITLE)
    expect(formatDocumentTitle(undefined)).toBe(BASE_DOCUMENT_TITLE)
  })
})

describe('useDocumentTitle', () => {
  it('sets document.title from the page title on mount', () => {
    renderHook(() => useDocumentTitle('Documentation'))
    expect(document.title).toBe('Documentation · TestLookup')
  })

  it('updates document.title when the page title changes', () => {
    const { rerender } = renderHook(({ t }) => useDocumentTitle(t), {
      initialProps: { t: 'Runs' },
    })
    expect(document.title).toBe('Runs · TestLookup')
    rerender({ t: 'Trends' })
    expect(document.title).toBe('Trends · TestLookup')
  })

  it('leaves the last title in place on unmount rather than restoring', () => {
    const { unmount } = renderHook(() => useDocumentTitle('Flaky Coach'))
    expect(document.title).toBe('Flaky Coach · TestLookup')
    unmount()
    // No restore-on-unmount: the outgoing page keeps its title until the next
    // page's heading overwrites it, avoiding a base-title flash mid-navigation.
    expect(document.title).toBe('Flaky Coach · TestLookup')
  })
})

describe('route document titles', () => {
  it('covers static and dynamic routes that do not render PageHeader', () => {
    expect(routeDocumentTitle('/overview')).toBe('Dashboard')
    expect(routeDocumentTitle('/live')).toBe('Live Execution')
    expect(routeDocumentTitle('/runs/run-1/tests/test-2')).toBe('Test Case')
    expect(routeDocumentTitle('/unknown')).toBe(BASE_DOCUMENT_TITLE)
  })

  it('replaces a previous page title when navigation reaches a custom-header route', () => {
    let navigate: ReturnType<typeof useNavigate>
    const wrapper = ({ children }: PropsWithChildren) =>
      createElement(MemoryRouter, { initialEntries: ['/failures'] }, children)
    const { result } = renderHook(() => {
      navigate = useNavigate()
      useRouteDocumentTitle()
      return navigate
    }, { wrapper })

    expect(document.title).toBe('Failure Analysis · TestLookup')
    act(() => result.current('/live'))
    expect(document.title).toBe('Live Execution · TestLookup')
  })
})
