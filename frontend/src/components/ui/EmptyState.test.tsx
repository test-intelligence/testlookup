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
})
