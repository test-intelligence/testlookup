/**
 * a11y M5: with `viz_multi_filters` on, one ArrowDown on the TopBar release
 * `<select>` silently replaced a multi-release selection with one release (a
 * native select commits the next option on ArrowDown). Same trap in the page
 * suite `SuiteFilterSelect`. With several values selected the header controls
 * are read-only summaries now; no keystroke on them changes the selection.
 *
 * `pressArrowDown` models the browser: a keydown on a native `<select>` that
 * nobody prevented selects the next enabled option and fires `change` (jsdom
 * implements neither), so the test fails against a `<select>` the way a real
 * keyboard user's selection did.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocked = vi.hoisted(() => ({
  releasesResult: { data: undefined as unknown, isLoading: true, isValidating: true },
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => mocked.releasesResult }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))

import { ReleasePicker } from './ReleasePicker'
import SuiteFilterSelect from '@/components/ui/SuiteFilterSelect'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { selectReleaseIds, useReleaseStore } from '@/store/releaseStore'
import { loadMultiFiltersRuntime } from './multiFiltersRuntimeLoader'

// In the app the flag reads 'on' only once the lazy multi-filters runtime
// (which holds the TopBar summary button) has loaded — it is what publishes
// 'on'. This file sets the flag directly, so it loads the runtime first too.
beforeAll(async () => {
  await loadMultiFiltersRuntime()
})

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const R1 = '11111111-0000-4000-8000-000000000001'
const R2 = '22222222-0000-4000-8000-000000000002'
const R3 = '33333333-0000-4000-8000-000000000003'

function pressArrowDown(el: HTMLElement) {
  const notPrevented = fireEvent.keyDown(el, { key: 'ArrowDown', code: 'ArrowDown' })
  if (!notPrevented || !(el instanceof HTMLSelectElement)) return
  const options = Array.from(el.options)
  const next = options.slice(el.selectedIndex + 1).find((o) => !o.disabled)
  if (!next) return
  fireEvent.change(el, { target: { value: next.value } })
}

function renderPicker() {
  return render(
    <MemoryRouter initialEntries={['/trends']}>
      <ReleasePicker />
    </MemoryRouter>,
  )
}

const control = () => screen.getByLabelText(/^Filter by release/)

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  useProjectStore.setState({ activeProjectId: PROJECT })
  mocked.releasesResult = {
    data: { items: [{ id: R1, name: '2026.09' }, { id: R2, name: '2026.10' }, { id: R3, name: '2026.11' }], total: 3 },
    isLoading: false,
    isValidating: false,
  }
  useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
  useReleaseStore.getState().setActiveReleases([R1, R2], PROJECT)
})

describe('TopBar ReleasePicker with viz_multi_filters on', () => {
  it('one ArrowDown does not replace a two-release selection', () => {
    renderPicker()
    const el = control()
    el.focus()
    pressArrowDown(el)
    pressArrowDown(el)
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
    expect(el.tagName).toBe('BUTTON')
    expect(el).toHaveTextContent('2 releases')
  })

  it('activating it moves focus to the report filter bar release control when the page has one', () => {
    const bar = document.createElement('div')
    bar.innerHTML = '<div data-report-filter="release"><button type="button">Release: 2 selected</button></div>'
    document.body.appendChild(bar)
    renderPicker()
    fireEvent.click(control())
    expect(document.activeElement).toBe(bar.querySelector('button'))
    expect(screen.queryByRole('menu')).toBeNull()
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
  })

  it('without a filter bar it opens a menu; arrow keys move focus only, a click picks one release', () => {
    renderPicker()
    fireEvent.click(control())
    const menu = screen.getByRole('menu')
    const items = screen.getAllByRole('menuitemradio')
    expect(items.map((i) => i.textContent)).toEqual(['All releases', '2026.09', '2026.10', '2026.11', '— Unattributed —'])
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(control())
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R1, R2])

    fireEvent.click(control())
    fireEvent.click(screen.getByRole('menuitemradio', { name: '2026.11' }))
    expect(selectReleaseIds(useReleaseStore.getState())).toEqual([R3])
  })

  it('flag OFF: still the legacy native select (unchanged)', () => {
    act(() => { useMultiFiltersFlagStore.setState({ enabled: false, resolved: true }) })
    useReleaseStore.getState().setActiveRelease(R1, PROJECT)
    renderPicker()
    expect(control().tagName).toBe('SELECT')
    expect(screen.getByRole('combobox', { name: 'Filter by release' })).toHaveValue(R1)
  })
})

describe('SuiteFilterSelect with several global suites', () => {
  it('one ArrowDown does not collapse "2 suites" into one suite', () => {
    const onChange = vi.fn()
    render(
      <SuiteFilterSelect value="" multiLabel="2 suites" onChange={onChange} options={['cart', 'payments', 'search']} />,
    )
    const el = screen.getByLabelText(/^Test suite/)
    el.focus()
    pressArrowDown(el)
    pressArrowDown(el)
    expect(onChange).not.toHaveBeenCalled()
    expect(el.tagName).toBe('BUTTON')
    expect(el).toHaveTextContent('2 suites')
  })

  it('activating it moves focus to the report filter bar suite control when present', () => {
    const bar = document.createElement('div')
    bar.innerHTML = '<button type="button" data-report-filter="suite">Suite: 2 selected</button>'
    document.body.appendChild(bar)
    const onChange = vi.fn()
    render(<SuiteFilterSelect value="" multiLabel="2 suites" onChange={onChange} options={['cart', 'payments']} />)
    fireEvent.click(screen.getByLabelText(/^Test suite/))
    expect(document.activeElement).toBe(bar.querySelector('button'))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('one or no suite: the ordinary select, as before', () => {
    const onChange = vi.fn()
    render(<SuiteFilterSelect value="cart" onChange={onChange} options={['cart', 'payments']} />)
    const el = screen.getByLabelText('Test suite')
    expect(el.tagName).toBe('SELECT')
    expect(el).toHaveValue('cart')
  })
})

describe('SuiteFilterSelect names a selected suite the page does not list (m4)', () => {
  it('shows the selected suite, not "All suites", when it is missing from the options', () => {
    render(<SuiteFilterSelect value="legacy-suite" onChange={vi.fn()} options={['cart', 'payments']} />)
    const el = screen.getByLabelText('Test suite') as HTMLSelectElement
    expect(el.value).toBe('legacy-suite')
    expect(el.selectedOptions[0].textContent).toBe('legacy-suite')
  })
})
