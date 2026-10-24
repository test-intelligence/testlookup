import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SearchFallbackBanner from './SearchFallbackBanner'

describe('SearchFallbackBanner (W12 SRCH-01)', () => {
  it('an outage reads in warning ink, the server\'s sentence verbatim', () => {
    render(<SearchFallbackBanner reason="semantic_unavailable" message="Semantic index unavailable — showing keyword results." />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('Semantic index unavailable — showing keyword results.')
    expect(banner.className).toContain('--ui-warning-ink')
    expect(banner).toHaveAttribute('data-search-fallback', 'semantic_unavailable')
  })

  it.each(['no_semantic_match', 'no_text'])('%s reads in info ink', (reason) => {
    render(<SearchFallbackBanner reason={reason} message="sentence" />)
    expect(screen.getByRole('status').className).toContain('--ui-info-ink')
  })

  it('no reason (or no sentence), no banner', () => {
    const { container, rerender } = render(<SearchFallbackBanner reason={null} message="x" />)
    expect(container).toBeEmptyDOMElement()
    rerender(<SearchFallbackBanner reason="no_text" message={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('the sentence is text, never markup', () => {
    render(<SearchFallbackBanner reason="no_text" message={'<img src=x onerror=alert(1)>'} />)
    expect(screen.getByRole('status').querySelector('img')).toBeNull()
  })
})
