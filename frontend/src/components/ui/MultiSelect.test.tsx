/**
 * VIZ-109: MultiSelect's keyboard and ARIA contract, driven the way a keyboard
 * user drives it (mirrors modalKeyboardContract.test.tsx): real trigger, real
 * focus, keys dispatched at whatever holds focus.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MultiSelect, { MULTISELECT_LABEL_MAX_CHARS, type MultiSelectOption, type MultiSelectProps } from './MultiSelect'

const releases = (n: number): MultiSelectOption[] =>
  Array.from({ length: n }, (_, i) => ({ value: `r${i + 1}`, label: `Release ${i + 1}`, count: (i + 1) * 10 }))

type HarnessProps = Partial<Omit<MultiSelectProps, 'value' | 'onChange'>> & {
  initial?: string[]
  onChange?: (next: string[]) => void
}

function Harness({ initial = [], onChange, options = releases(60), label = 'Release', ...rest }: HarnessProps) {
  const [value, setValue] = useState<string[]>(initial)
  return (
    <MultiSelect
      label={label}
      options={options}
      value={value}
      onChange={(next) => {
        onChange?.(next)
        setValue(next)
      }}
      data-testid="ms"
      {...rest}
    />
  )
}

const trigger = () => screen.getByRole('button', { name: /^Release/ })
const combobox = () => screen.getByRole('combobox', { name: 'Filter Release' })
const listbox = () => screen.getByRole('listbox', { name: 'Release' })
const options = () => within(listbox()).getAllByRole('option')
const press = (key: string, init: Partial<KeyboardEventInit> = {}) =>
  fireEvent.keyDown(document.activeElement ?? document.body, { key, ...init })

function openWithKeyboard() {
  trigger().focus()
  fireEvent.click(trigger()) // Enter/Space on a <button> is a click
  expect(combobox()).toHaveFocus()
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MultiSelect — closed control', () => {
  it('is a popup trigger that shows the selection count and an active look', () => {
    const { rerender } = render(<Harness />)
    expect(trigger()).toHaveAttribute('aria-haspopup', 'dialog')
    expect(trigger()).toHaveAttribute('aria-expanded', 'false')
    expect(trigger()).toHaveAttribute('data-active', 'false')
    expect(screen.queryByTestId('ms-count')).toBeNull()

    rerender(<Harness key="b" initial={['r1', 'r2']} />)
    expect(trigger()).toHaveAttribute('data-active', 'true')
    expect(screen.getByTestId('ms-count')).toHaveTextContent('2')
    expect(trigger()).toHaveAccessibleName('Release, 2 selected')
  })

  it('does not open when disabled, and says why', () => {
    render(<Harness disabled disabledReason="Pick a project first" />)
    expect(trigger()).toHaveAttribute('aria-disabled', 'true')
    expect(trigger()).toHaveAccessibleDescription('Pick a project first')
    fireEvent.click(trigger())
    press('ArrowDown')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(trigger()).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('MultiSelect — combobox with listbox', () => {
  it('opens into a focused filter field wired to a multi-select listbox', () => {
    render(<Harness />)
    openWithKeyboard()
    expect(trigger()).toHaveAttribute('aria-expanded', 'true')
    expect(combobox()).toHaveAttribute('aria-controls', listbox().id)
    expect(combobox()).toHaveAttribute('aria-autocomplete', 'list')
    expect(combobox()).not.toHaveAttribute('aria-activedescendant')
    expect(listbox()).toHaveAttribute('aria-multiselectable', 'true')
  })

  it('ArrowDown also opens it from the trigger', () => {
    render(<Harness />)
    trigger().focus()
    press('ArrowDown')
    expect(combobox()).toHaveFocus()
  })

  it('lists 10 of 60, then "Show more" reveals the rest and keeps keyboard focus', () => {
    render(<Harness />)
    openWithKeyboard()
    expect(options()).toHaveLength(10)
    const more = screen.getByRole('button', { name: 'Show more (50)' })
    fireEvent.click(more)
    expect(options()).toHaveLength(60)
    expect(screen.queryByRole('button', { name: /Show more/ })).toBeNull()
    expect(combobox()).toHaveFocus()
    // The first newly revealed option is active.
    expect(combobox()).toHaveAttribute('aria-activedescendant', options()[10].id)
  })

  it('moves the active option with the arrows, Home and End', () => {
    render(<Harness />)
    openWithKeyboard()
    const activeId = () => combobox().getAttribute('aria-activedescendant')

    press('ArrowDown')
    expect(activeId()).toBe(options()[0].id)
    press('ArrowDown')
    press('ArrowDown')
    expect(activeId()).toBe(options()[2].id)
    press('ArrowUp')
    expect(activeId()).toBe(options()[1].id)
    press('End')
    expect(activeId()).toBe(options()[9].id)
    press('ArrowDown') // stays on the last shown option
    expect(activeId()).toBe(options()[9].id)
    press('Home')
    expect(activeId()).toBe(options()[0].id)
    press('ArrowUp')
    expect(activeId()).toBe(options()[0].id)
  })

  it('Home/End move the caret, not the option, until an option is active', () => {
    render(<Harness />)
    openWithKeyboard()
    expect(press('Home')).toBe(true) // not prevented
    expect(combobox()).not.toHaveAttribute('aria-activedescendant')
  })

  it('type-ahead filters; Space types until an option is active, then toggles', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    openWithKeyboard()

    fireEvent.change(combobox(), { target: { value: 'Release 4' } })
    // "Release 4" and "Release 40".."Release 49": 11 matches, the first 10 shown.
    expect(options().map((o) => o.getAttribute('data-value'))).toEqual([
      'r4', 'r40', 'r41', 'r42', 'r43', 'r44', 'r45', 'r46', 'r47', 'r48',
    ])
    expect(screen.getByRole('button', { name: 'Show more (1)' })).toBeInTheDocument()

    // No option active: Space is a character, not a toggle.
    expect(press(' ')).toBe(true)
    expect(onChange).not.toHaveBeenCalled()

    press('ArrowDown')
    expect(press(' ')).toBe(false) // prevented: it toggled
    expect(onChange).toHaveBeenLastCalledWith(['r4'])
    expect(options()[0]).toHaveAttribute('aria-selected', 'true')

    press('ArrowDown')
    press('Enter')
    expect(onChange).toHaveBeenLastCalledWith(['r4', 'r40'])

    // Space again on a selected option deselects it.
    press(' ')
    expect(onChange).toHaveBeenLastCalledWith(['r4'])

    // Typing again resets the active option, so Space types again.
    fireEvent.change(combobox(), { target: { value: 'Release 4 ' } })
    expect(combobox()).not.toHaveAttribute('aria-activedescendant')
  })

  it('Enter never submits an enclosing form', () => {
    const onSubmit = vi.fn((event: Event) => event.preventDefault())
    render(
      <form onSubmit={(e) => onSubmit(e.nativeEvent)}>
        <Harness />
      </form>,
    )
    openWithKeyboard()
    expect(press('Enter')).toBe(false)
  })

  it('shows "No matches" for a filter that matches nothing', () => {
    render(<Harness />)
    openWithKeyboard()
    fireEvent.change(combobox(), { target: { value: 'zzz' } })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(screen.getByText('No matches')).toBeInTheDocument()
    expect(press('ArrowDown')).toBe(false)
    expect(combobox()).not.toHaveAttribute('aria-activedescendant')
  })

  it('clicking an option toggles it and keeps the popover open', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    openWithKeyboard()
    fireEvent.mouseDown(options()[2])
    fireEvent.click(options()[2])
    expect(onChange).toHaveBeenLastCalledWith(['r3'])
    expect(listbox()).toBeInTheDocument()
  })
})

describe('MultiSelect — cap', () => {
  it('refuses a selection past the cap, marks the rest aria-disabled and announces it', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} max={2} />)
    openWithKeyboard()
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('')

    press('ArrowDown')
    press(' ')
    press('ArrowDown')
    press(' ')
    expect(onChange).toHaveBeenLastCalledWith(['r1', 'r2'])
    expect(status).toHaveTextContent('Limit reached (2)')
    expect(options()[2]).toHaveAttribute('aria-disabled', 'true')
    expect(options()[0]).not.toHaveAttribute('aria-disabled')

    // Space and a click on a third option both do nothing.
    press('ArrowDown')
    press(' ')
    fireEvent.click(options()[3])
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('ms-count')).toHaveTextContent('2')

    // Deselecting frees a slot and clears the limit message.
    press('Home')
    press(' ')
    expect(onChange).toHaveBeenLastCalledWith(['r2'])
    expect(status).toHaveTextContent('Release 1 deselected, 1 of 2')
    expect(status).not.toHaveTextContent('Limit reached')
    expect(options()[2]).not.toHaveAttribute('aria-disabled')
  })

  it('"Select all" fills only up to the cap', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} max={3} initial={['r5']} />)
    openWithKeyboard()
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(onChange).toHaveBeenLastCalledWith(['r5', 'r1', 'r2'])
    expect(screen.getByRole('button', { name: 'Select all' })).toBeDisabled()
    expect(combobox()).toHaveFocus()
  })
})

describe('MultiSelect — bulk actions, counts, disabled options', () => {
  const opts: MultiSelectOption[] = [
    { value: 'a', label: 'Alpha', count: 1234567 },
    { value: 'b', label: 'Beta', count: 0 },
    { value: 'c', label: 'Gamma', disabled: true, disabledReason: 'No runs in window' },
    { value: 'd', label: 'Delta' },
  ]

  it('shows compact counts with the exact number in title', () => {
    render(<Harness options={opts} />)
    openWithKeyboard()
    const alpha = options()[0]
    const count = within(alpha).getByText('1.23M')
    expect(count).toHaveAttribute('title', '1,234,567')
    expect(within(options()[1]).getByText('0')).toBeInTheDocument()
  })

  it('a disabled option says why and cannot be toggled', () => {
    const onChange = vi.fn()
    render(<Harness options={opts} onChange={onChange} />)
    openWithKeyboard()
    const gamma = options()[2]
    expect(gamma).toHaveAttribute('aria-disabled', 'true')
    expect(gamma).toHaveTextContent('Gamma(No runs in window)')
    press('ArrowDown')
    press('ArrowDown')
    press('ArrowDown')
    press(' ')
    fireEvent.click(gamma)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('"Select all" skips disabled options; "Select none" clears but keeps a locked selection', () => {
    const onChange = vi.fn()
    render(<Harness options={opts} onChange={onChange} initial={['c']} />)
    openWithKeyboard()
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(onChange).toHaveBeenLastCalledWith(['c', 'a', 'b', 'd'])
    fireEvent.click(screen.getByRole('button', { name: 'Select none' }))
    expect(onChange).toHaveBeenLastCalledWith(['c'])
    expect(screen.getByRole('button', { name: 'Select none' })).toBeDisabled()
  })

  it('"Select matching" adds only what the filter shows', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    openWithKeyboard()
    fireEvent.change(combobox(), { target: { value: 'Release 5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Select matching' }))
    expect(onChange).toHaveBeenLastCalledWith(['r5', 'r50', 'r51', 'r52', 'r53', 'r54', 'r55', 'r56', 'r57', 'r58', 'r59'])
  })

  it('lists the options selected at opening first', () => {
    render(<Harness initial={['r30']} />)
    openWithKeyboard()
    expect(options()[0]).toHaveAttribute('data-value', 'r30')
    expect(options()[0]).toHaveAttribute('aria-selected', 'true')
    expect(options()[1]).toHaveAttribute('aria-selected', 'false')
  })
})

describe('MultiSelect — labels are text', () => {
  it('renders a hostile label as literal text, never as markup', () => {
    const hostile = '<img src="x" onerror="window.__pwned=1"><b>bold</b>'
    render(<Harness options={[{ value: 'x', label: hostile }]} />)
    openWithKeyboard()
    const option = options()[0]
    expect(option.querySelector('img')).toBeNull()
    expect(option.querySelector('b')).toBeNull()
    expect(option).toHaveTextContent(hostile)
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
  })

  it('middle-truncates a 300-character label to 60 and keeps the full text in title', () => {
    const long = `checkout-${'m'.repeat(282)}-payments`
    expect(long).toHaveLength(300)
    render(<Harness options={[{ value: 'long', label: long }]} />)
    openWithKeyboard()
    const option = options()[0]
    const shown = option.textContent ?? ''
    expect(Array.from(shown)).toHaveLength(MULTISELECT_LABEL_MAX_CHARS)
    expect(shown.startsWith('checkout-')).toBe(true)
    expect(shown.endsWith('-payments')).toBe(true)
    // The tooltip is on the visible label; the option's NAME is the full label.
    expect(within(option).getByTitle(long)).toHaveTextContent(shown)
    expect(option).toHaveAccessibleName(long)
  })
})

describe('MultiSelect — closing', () => {
  it('Escape closes, returns focus to the trigger, and does not reach an enclosing dialog', () => {
    const outer = vi.fn()
    document.addEventListener('keydown', outer)
    try {
      render(<Harness />)
      openWithKeyboard()
      press('ArrowDown')
      press('Escape')
      expect(screen.queryByRole('listbox')).toBeNull()
      expect(trigger()).toHaveAttribute('aria-expanded', 'false')
      expect(trigger()).toHaveFocus()
      expect(outer.mock.calls.filter(([e]) => (e as KeyboardEvent).key === 'Escape')).toHaveLength(0)
    } finally {
      document.removeEventListener('keydown', outer)
    }
  })

  it('Tab past the last control with nothing after the trigger, or Shift+Tab before the field, closes back to the trigger', () => {
    render(<Harness />)
    openWithKeyboard()
    screen.getByRole('button', { name: /Show more/ }).focus()
    expect(press('Tab')).toBe(false)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(trigger()).toHaveFocus()

    fireEvent.click(trigger())
    expect(combobox()).toHaveFocus()
    press('Tab', { shiftKey: true })
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(trigger()).toHaveFocus()
  })

  it('an outside press closes it; a press inside the portalled popover does not', () => {
    render(
      <>
        <Harness />
        <button type="button">Elsewhere</button>
      </>,
    )
    openWithKeyboard()
    fireEvent.mouseDown(options()[0])
    fireEvent.mouseDown(combobox())
    expect(listbox()).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Elsewhere' }))
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('the trigger toggles it closed', () => {
    render(<Harness />)
    openWithKeyboard()
    fireEvent.mouseDown(trigger())
    fireEvent.click(trigger())
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})

describe('MultiSelect — where the popover lives', () => {
  it('portals to <body> by default', () => {
    const { container } = render(<Harness />)
    openWithKeyboard()
    const popover = document.querySelector('[data-multiselect-popover]')
    expect(popover?.parentElement).toBe(document.body)
    expect(container.contains(popover)).toBe(false)
  })

  it('stays inside an enclosing modal dialog', () => {
    render(
      <div role="dialog" aria-modal="true" aria-label="Filters" data-testid="dialog">
        <Harness />
      </div>,
    )
    openWithKeyboard()
    const popover = document.querySelector('[data-multiselect-popover]')
    expect(popover?.parentElement).toBe(screen.getByTestId('dialog'))
  })

  it('stays inside the fullscreen element that holds the trigger', () => {
    render(
      <div data-testid="fs">
        <Harness />
      </div>,
    )
    const fs = screen.getByTestId('fs')
    // jsdom has no Fullscreen API; stand in for it on the instance.
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fs })
    try {
      openWithKeyboard()
      expect(document.querySelector('[data-multiselect-popover]')?.parentElement).toBe(fs)
    } finally {
      delete (document as { fullscreenElement?: unknown }).fullscreenElement
    }
  })
})

describe('MultiSelect — long lists are windowed above 200', () => {
  it('renders a window with setsize/posinset and keeps the active option rendered', () => {
    render(<Harness options={releases(500)} />)
    openWithKeyboard()
    fireEvent.click(screen.getByRole('button', { name: 'Show more (490)' }))

    const rendered = options()
    expect(rendered.length).toBeGreaterThan(10)
    expect(rendered.length).toBeLessThan(60)
    expect(rendered[0]).toHaveAttribute('aria-setsize', '500')
    expect(rendered[0]).toHaveAttribute('aria-posinset', '1')
    // The scroller is fixed at ten rows; the listbox inside it carries the spacers.
    expect(listbox().parentElement).toHaveAttribute('data-multiselect-scroller')
    expect(listbox().parentElement?.style.height).toBe('320px')

    act(() => {
      press('End')
    })
    const activeId = combobox().getAttribute('aria-activedescendant')
    const active = activeId ? document.getElementById(activeId) : null
    expect(active).not.toBeNull()
    expect(active).toHaveAttribute('aria-posinset', '500')
    expect(active).toHaveAttribute('data-value', 'r500')
    expect(options().length).toBeLessThan(60)
    // Rows before the window are stood in for by padding, not dropped.
    expect(parseInt(listbox().style.paddingTop, 10)).toBeGreaterThan(0)
  })

  it('does not window at or below 200', () => {
    render(<Harness options={releases(200)} />)
    openWithKeyboard()
    fireEvent.click(screen.getByRole('button', { name: 'Show more (190)' }))
    expect(options()).toHaveLength(200)
  })

  it('a mouse scroll away from the active option pulls it into the rendered window', () => {
    const onChange = vi.fn()
    render(<Harness options={releases(500)} onChange={onChange} />)
    openWithKeyboard()
    fireEvent.click(screen.getByRole('button', { name: 'Show more (490)' }))
    press('Home')
    press('ArrowDown')
    const scroller = listbox().parentElement as HTMLElement
    act(() => {
      scroller.scrollTop = 300 * 32
      fireEvent.scroll(scroller)
    })
    // aria-activedescendant must name an option that is in the document...
    const activeId = combobox().getAttribute('aria-activedescendant')
    expect(activeId).toBeTruthy()
    const active = document.getElementById(activeId ?? '')
    expect(active).not.toBeNull()
    expect(listbox().contains(active)).toBe(true)
    // ...and Space toggles THAT option, not an invisible one.
    press(' ')
    expect(onChange).toHaveBeenLastCalledWith([active?.getAttribute('data-value')])
  })
})

describe('MultiSelect — accessible names of options (review fix 1)', () => {
  const opts: MultiSelectOption[] = [
    { value: 'a', label: 'Alpha', count: 1234567 },
    { value: 'b', label: 'Beta', count: 0 },
    { value: 'c', label: 'Gamma', count: 5, disabled: true, disabledReason: 'No runs in window' },
    { value: 'd', label: 'Delta' },
  ]

  it('separates the count and the disabled reason from the label', () => {
    render(<Harness options={opts} />)
    openWithKeyboard()
    expect(screen.getByRole('option', { name: 'Alpha, 1,234,567' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Beta, 0' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Gamma, 5, No runs in window' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Delta' })).toBeInTheDocument()
  })

  it('names a truncated option by its FULL label, never the "…" form', () => {
    const long = `checkout-${'m'.repeat(282)}-payments`
    render(<Harness options={[{ value: 'long', label: long, count: 3 }]} />)
    openWithKeyboard()
    const option = options()[0]
    expect(option.textContent).toContain('…')
    expect(option).toHaveAccessibleName(`${long}, 3`)
  })
})

describe('MultiSelect — popover fits the viewport (review fix 2)', () => {
  it('caps the popover at the viewport height, less the gutters, and lets it scroll', () => {
    render(<Harness />)
    openWithKeyboard()
    const popover = document.querySelector<HTMLElement>('[data-multiselect-popover]')
    expect(popover?.style.maxHeight).toBe(`${window.innerHeight - 16}px`)
    expect(popover?.className).toMatch(/\boverflow-y-auto\b/)
  })
})

describe('MultiSelect — announcements (review fix 6)', () => {
  const liveText = () =>
    Array.from(document.querySelectorAll('[aria-live], [role="status"], [role="alert"]'))
      .map((node) => node.textContent ?? '')
      .join(' | ')

  it('announces each toggle once in the polite live region', () => {
    render(<Harness />)
    openWithKeyboard()
    press('ArrowDown')
    press(' ')
    expect(screen.getByRole('status')).toHaveTextContent('Release 1 selected, 1 of 60')
    expect(liveText().split('Release 1 selected').length - 1).toBe(1)
    press(' ')
    expect(screen.getByRole('status')).toHaveTextContent('Release 1 deselected, 0 of 60')
    expect(liveText()).not.toContain('Release 1 selected')
  })
})

describe('MultiSelect — no matches (review fix 7)', () => {
  it('never points aria-controls at a listbox that is not there', () => {
    render(<Harness />)
    openWithKeyboard()
    fireEvent.change(combobox(), { target: { value: 'zzz' } })
    const controls = combobox().getAttribute('aria-controls')
    if (controls) expect(document.getElementById(controls)).not.toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('No matches')
  })
})

describe('MultiSelect — the popup is a dialog (review fix 8)', () => {
  it('the trigger announces a dialog, and the popup is a named dialog', () => {
    render(<Harness />)
    expect(trigger()).toHaveAttribute('aria-haspopup', 'dialog')
    openWithKeyboard()
    const popup = screen.getByRole('dialog', { name: 'Release options' })
    expect(trigger()).toHaveAttribute('aria-controls', popup.id)
    expect(popup).not.toHaveAttribute('aria-modal')
  })

  it('Tab past the last control closes and moves on to what follows the trigger', () => {
    render(
      <>
        <button type="button">Before</button>
        <Harness />
        <button type="button">After</button>
      </>,
    )
    openWithKeyboard()
    screen.getByRole('button', { name: /Show more/ }).focus()
    expect(press('Tab')).toBe(false)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByRole('button', { name: 'After' })).toHaveFocus()
  })
})

describe('MultiSelect — a disabled control shows why (review fix 9)', () => {
  it('the reason is visible text, not screen-reader-only', () => {
    render(<Harness disabled disabledReason="Pick a project first" />)
    const reason = screen.getByText('Pick a project first')
    expect(reason.className).not.toMatch(/\bsr-only\b/)
    expect(trigger()).toHaveAccessibleDescription('Pick a project first')
  })
})

describe('MultiSelect — controlled edge cases (review fix 11)', () => {
  const opts: MultiSelectOption[] = [
    { value: 'a', label: 'Alpha' },
    { value: 'b', label: 'Beta' },
    { value: 'c', label: 'Gamma' },
  ]

  it('lists a selected value that is not an option as "(not available)", deselectable, and outside the cap', () => {
    const onChange = vi.fn()
    render(<Harness options={opts} max={2} initial={['a', 'ghost']} onChange={onChange} />)
    openWithKeyboard()
    const ghost = screen.getByRole('option', { name: 'ghost (not available)' })
    expect(ghost).toHaveAttribute('aria-selected', 'true')
    // One of two real slots is used: Beta is still selectable.
    expect(screen.getByRole('option', { name: 'Beta' })).not.toHaveAttribute('aria-disabled')
    expect(screen.getByText(/1 of 2 selected/)).toHaveTextContent('1 of 2 selected, 1 not available')
    fireEvent.click(ghost)
    expect(onChange).toHaveBeenLastCalledWith(['a'])
  })

  it('never reads "3 of 2 selected" when unavailable values push the total past the cap', () => {
    render(<Harness options={opts} max={2} initial={['a', 'ghost-1', 'ghost-2']} />)
    openWithKeyboard()
    expect(screen.queryByText(/3 of 2 selected/)).toBeNull()
    expect(screen.getByText(/of 2 selected/)).toHaveTextContent('1 of 2 selected, 2 not available')
  })

  it('dedupes duplicate option values with a dev warning and no key clash', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<Harness options={[...opts, { value: 'a', label: 'Alpha again' }]} />)
    openWithKeyboard()
    expect(options().map((o) => o.getAttribute('data-value'))).toEqual(['a', 'b', 'c'])
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('duplicate option value "a"'))
    expect(error.mock.calls.filter(([m]) => String(m).includes('same key'))).toEqual([])
  })
})
