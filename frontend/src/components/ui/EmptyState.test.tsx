import { render, screen } from '@testing-library/react'
import { FileQuestion } from 'lucide-react'
import { describe, expect, it } from 'vitest'
import EmptyState from './EmptyState'

describe('EmptyState', () => {
  it('renders title and optional description', () => {
    render(
      <EmptyState
        icon={<FileQuestion />}
        title="No test runs"
        description="Upload a report to get started"
      />,
    )

    expect(screen.getByText('No test runs')).toBeInTheDocument()
    expect(screen.getByText('Upload a report to get started')).toBeInTheDocument()
  })

  it('renders action content', () => {
    render(
      <EmptyState
        icon={<FileQuestion />}
        title="No defects"
        action={<button>Add Defect</button>}
      />,
    )

    expect(screen.getByRole('button', { name: 'Add Defect' })).toBeInTheDocument()
  })

  // W29a UIX-11: one look for empty / unavailable. The neutral form keeps
  // today's markup (its 73 call sites unchanged); the warning tone and the
  // compact form are new.
  it('neutral, not compact: the same elements and class strings as before', () => {
    const { container } = render(
      <EmptyState icon={<FileQuestion />} title="No test runs" description="Upload a report" action={<button>Upload</button>} />,
    )
    const root = container.firstChild as HTMLElement
    expect(root.getAttribute('class')).toBe('flex flex-col items-center justify-center py-12 text-center')
    expect(root.hasAttribute('role')).toBe(false)
    expect(root.hasAttribute('data-testid')).toBe(false)
    expect(Array.from(root.children).map((el) => [el.tagName, el.getAttribute('class')])).toEqual([
      ['DIV', 'p-4 bg-[var(--color-bg-secondary)] rounded-2xl mb-4 text-[var(--color-text-muted)]'],
      ['H3', 'text-lg font-semibold text-[var(--color-text-secondary)] mb-1'],
      ['P', 'text-sm text-[var(--color-text-muted)] max-w-sm mb-4'],
      ['BUTTON', null],
    ])
  })

  it('warning: the icon tile reads the warning tokens, the title stays neutral (no orange headline)', () => {
    const { container } = render(
      <EmptyState tone="warning" role="alert" testId="partial" icon={<FileQuestion />} title="Results are partial" />,
    )
    const root = screen.getByTestId('partial')
    expect(root).toHaveAttribute('role', 'alert')
    const tile = root.firstElementChild as HTMLElement
    expect(tile.style.background).toBe('var(--ui-warning-bg)')
    expect(tile.style.color).toBe('var(--ui-warning)')
    expect(screen.getByRole('heading', { name: 'Results are partial' })).toHaveClass('text-[var(--color-text-secondary)]')
    expect(container.innerHTML).not.toContain('--status-broken')
  })

  it('compact: one muted line holding the title, the action inline', () => {
    const { container } = render(<EmptyState compact title="No comments yet." action={<button>Add one</button>} />)
    const line = container.firstChild as HTMLElement
    expect(line.tagName).toBe('P')
    expect(line).toHaveClass('py-3', 'text-sm', 'text-[var(--color-text-muted)]')
    expect(line).toHaveTextContent('No comments yet.')
    expect(line.querySelector('button')).toHaveTextContent('Add one')
    expect(container.querySelector('h3')).toBeNull()
  })
})
