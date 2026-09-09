import { fireEvent, render, screen } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import {
  DocumentTitleRouteKeyContext,
  useRouteDocumentTitle,
} from '@/hooks/useDocumentTitle'
import PageHeader from './PageHeader'

function RouteTitleHarness() {
  const routeKey = useRouteDocumentTitle()
  return (
    <DocumentTitleRouteKeyContext.Provider value={routeKey}>
      <PageHeader title="Agent Pipeline" />
      <Link to="/agents/run/run-1">Open run</Link>
    </DocumentTitleRouteKeyContext.Provider>
  )
}

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

  it('sets the browser-tab title from the page heading', () => {
    render(<PageHeader title="Release Gate" />)

    expect(document.title).toBe('Release Gate · TestLookup')
  })

  it('keeps the page heading title when the same page instance handles a new route', () => {
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes><Route path="*" element={<RouteTitleHarness />} /></Routes>
      </MemoryRouter>,
    )
    expect(document.title).toBe('Agent Pipeline · TestLookup')

    fireEvent.click(screen.getByRole('link', { name: 'Open run' }))
    expect(document.title).toBe('Agent Pipeline · TestLookup')
  })
})
