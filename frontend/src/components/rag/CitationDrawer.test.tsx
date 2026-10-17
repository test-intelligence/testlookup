import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import CitationDrawer from './CitationDrawer'
import type { Citation } from '@/types/rag-generation'

function citation(canonicalUrl: string): Citation {
  return {
    case_index: 0,
    vector_id: 'vec-1',
    source_id: 'source-1',
    source_title: '<script>source</script>',
    section_heading: 'Requirement',
    chunk_text_preview: '<img src=x onerror=boom>',
    relevance_score: 0.9,
    canonical_url: canonicalUrl,
  }
}

describe('CitationDrawer source safety', () => {
  it('renders an HTTP source as an isolated external link and escapes evidence text', () => {
    render(<CitationDrawer citations={[citation('https://docs.example.test/r/1')]} caseIndex={0} onClose={vi.fn()} />)
    const link = screen.getByRole('link', { name: 'Open source' })
    expect(link).toHaveAttribute('href', 'https://docs.example.test/r/1')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('<script>source</script>')).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
  })

  it('does not render a javascript source URL', () => {
    render(<CitationDrawer citations={[citation('javascript:alert(1)')]} caseIndex={0} onClose={vi.fn()} />)
    expect(screen.queryByRole('link', { name: 'Open source' })).not.toBeInTheDocument()
  })
})

// W27b PAN-05: in the page, not a fixed edge panel; named by its heading, its X labelled.
describe('CitationDrawer in the page', () => {
  it('is an aside named by its heading, with a Close X, and no fixed or full-height frame', () => {
    const onClose = vi.fn()
    render(<CitationDrawer citations={[citation('https://docs.example.test/r/1')]} caseIndex={0} onClose={onClose} />)
    const aside = screen.getByRole('complementary', { name: 'Citations (1)' })
    expect(aside).toHaveAttribute('data-in-page', 'form')
    expect(aside.className).not.toMatch(/\bfixed\b|inset-y-0|\bw-96\b/)
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
