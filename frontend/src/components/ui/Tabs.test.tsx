import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import Tabs from './Tabs'
import { useTabParam } from './useTabParam'

const IDS = ['overview', 'failures', 'history'] as const

describe('Tabs', () => {
  it('renders one tab per item, marks the selected one, and reports clicks', () => {
    const onChange = vi.fn()
    render(
      <Tabs
        ariaLabel="Run sections"
        value="failures"
        onChange={onChange}
        items={[
          { id: 'overview', label: 'Overview' },
          { id: 'failures', label: 'Failures', count: 0 },
          { id: 'history', label: 'History', count: 12 },
        ]}
      />,
    )
    expect(screen.getByRole('tablist', { name: 'Run sections' })).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(3)
    expect(screen.getByRole('tab', { name: /Failures/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /Overview/ })).toHaveAttribute('aria-selected', 'false')
    // A zero count is shown: "0 failures" is information, not absence.
    expect(screen.getByRole('tab', { name: /Failures/ }).querySelector('[data-tab-count]')?.textContent).toBe('0')
    expect(screen.getByRole('tab', { name: /Overview/ }).querySelector('[data-tab-count]')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: /History/ }))
    expect(onChange).toHaveBeenCalledWith('history')
  })
})

describe('useTabParam', () => {
  function setup(initial: string) {
    let search = ''
    const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
    const hook = renderHook(
      () => {
        const tab = useTabParam(IDS, 'overview')
        search = useLocation().search
        return tab
      },
      { wrapper },
    )
    return { hook, search: () => search }
  }

  it('reads ?tab=, and falls back for a missing or unknown value', () => {
    expect(setup('/runs/1?tab=history').hook.result.current[0]).toBe('history')
    expect(setup('/runs/1').hook.result.current[0]).toBe('overview')
    expect(setup('/runs/1?tab=nonsense').hook.result.current[0]).toBe('overview')
  })

  it('writes the tab to the URL, keeps other params, and drops the key for the default', () => {
    const { hook, search } = setup('/runs/1?days=7')
    act(() => hook.result.current[1]('failures'))
    expect(hook.result.current[0]).toBe('failures')
    expect(new URLSearchParams(search()).get('tab')).toBe('failures')
    expect(new URLSearchParams(search()).get('days')).toBe('7')
    act(() => hook.result.current[1]('overview'))
    expect(new URLSearchParams(search()).has('tab')).toBe(false)
    expect(new URLSearchParams(search()).get('days')).toBe('7')
  })
})
