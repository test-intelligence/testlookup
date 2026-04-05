import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PageHeader from './PageHeader'

describe('PageHeader', () => {
  it('renders title and subtitle', () => {
    render(<PageHeader title="Dashboard" subtitle="Quality insights" />)

    expect(screen.getByText('Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Quality insights')).toBeInTheDocument()
  })

  it('renders action elements when provided', () => {
    render(<PageHeader title="Projects" actions={<button>New Project</button>} />)

    expect(screen.getByRole('button', { name: 'New Project' })).toBeInTheDocument()
  })
})
