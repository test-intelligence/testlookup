import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { avatarColorForName } from '@/components/ui/avatarColors'
import { CasePriorityTag, OwnerCell } from './TestManagementPage'

describe('Test Management owner cell', () => {
  it('shows the trimmed source-framework owner for automation rows', () => {
    render(<OwnerCell owner="  QA Platform  " userId={null} />)

    expect(screen.getByText('QA Platform')).toBeTruthy()
    expect(screen.queryByText('Unassigned')).toBeNull()
  })

  it('falls back to the catalog assignee or author when no source owner exists', () => {
    render(<OwnerCell owner="   " userId="12345678-abcd-4000-8000-000000000001" />)

    expect(screen.getByText('12345678')).toBeTruthy()
    expect(screen.queryByText('Unassigned')).toBeNull()
  })

  it('renders Unassigned only when neither ownership source exists', () => {
    render(<OwnerCell owner={null} userId={null} />)

    expect(screen.getByLabelText('Unassigned')).toBeTruthy()
  })
})

// W29a UIX-01 / UIX-13: the owner avatar reads the ONE avatar map — legible
// `fg` initials on its `bg`, never white text on a hard-coded gradient.
describe('Test Management owner avatar', () => {
  const avatar = () => document.querySelector('[data-owner-avatar]') as HTMLElement

  it.each([
    ['a named owner', { owner: 'Priya Nair', userId: null }, 'Priya Nair', 'PN'],
    ['an assignee id', { owner: null, userId: '12345678-abcd-4000-8000-000000000001' }, '12345678-abcd-4000-8000-000000000001', '12'],
  ] as const)('%s: initials in the map colour for its name', (_name, props, key, initials) => {
    render(<OwnerCell {...props} />)
    const c = avatarColorForName(key)
    expect(avatar()).toHaveTextContent(initials)
    expect(avatar().style.color).toBe(c.fg)
    expect(avatar().style.background).toBe(c.bg)
    expect(avatar().className).not.toMatch(/text-white/)
    expect(avatar().getAttribute('style') ?? '').not.toMatch(/gradient|#[0-9a-f]{3,8}\b/i)
  })

  it('one name always gets the same colour', () => {
    const { unmount } = render(<OwnerCell owner="QA Platform" userId={null} />)
    const first = avatar().getAttribute('style')
    unmount()
    render(<OwnerCell owner="QA Platform" userId={null} />)
    expect(avatar().getAttribute('style')).toBe(first)
  })

  // W29d UIX-04c rule 6: the initials circle is sized in em of its own text-2xs (11 px → 19.8 px), so it
  // follows the scale and presentation mode; the inline 18 px box and 9 px font are gone.
  it.each([
    ['a named owner', { owner: 'Priya Nair', userId: null }],
    ['an assignee id', { owner: null, userId: '12345678-abcd-4000-8000-000000000001' }],
    ['nobody (the dashed circle)', { owner: null, userId: null }],
  ] as const)('%s: the circle is h-[1.8em] w-[1.8em] text-2xs, with no inline width, height or font size', (_name, props) => {
    render(<OwnerCell {...props} />)
    const circle = (document.querySelector('[data-owner-avatar]')
      ?? screen.getByLabelText('Unassigned').querySelector('[aria-hidden]')) as HTMLElement
    for (const cls of ['h-[1.8em]', 'w-[1.8em]', 'text-2xs']) expect(circle).toHaveClass(cls)
    expect(circle.style.width).toBe('')
    expect(circle.style.height).toBe('')
    expect(circle.style.fontSize).toBe('')
    expect(circle.className).not.toMatch(/text-\[\d/)
  })
})

// W29a UIX-07: a case's priority tag is the ONE severity palette's P-level.
describe('Test Management priority tag', () => {
  const tag = () => document.querySelector('[data-priority-level]') as HTMLElement

  it.each([
    ['critical', 'P0', 'var(--ui-danger-ink)'],
    ['high', 'P1', 'var(--ui-warning-ink)'],
    ['medium', 'P2', 'var(--ui-info-ink)'],
    ['low', 'P3', 'var(--color-text-muted)'],
    ['blocker', 'P0', 'var(--ui-danger-ink)'],
    ['something-new', 'P3', 'var(--color-text-muted)'],
  ])('%s reads %s in the level\'s style', (priority, level, colour) => {
    render(<CasePriorityTag priority={priority} />)
    expect(tag()).toHaveTextContent(level)
    expect(tag()).toHaveAttribute('data-priority-level', level)
    expect(tag().style.color).toBe(colour)
    expect(tag().getAttribute('style') ?? '').not.toMatch(/--status-/)
  })
})
