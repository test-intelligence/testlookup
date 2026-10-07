/**
 * The page template's header (UX redesign P3/P5), asserted on a rendered page
 * that uses the REAL `PageHeader`: the page's one `<h1>` is the header's,
 * the header is compact, and its **?** opens the route's help topic.
 *
 * The topic is read from `helpTopics.ts` for the route rather than written
 * out, so a re-mapped route moves the expectation with it and a page that
 * hard-codes some other topic fails.
 */
import { screen } from '@testing-library/react'
import { expect } from 'vitest'
import { helpTopicParam } from '@/components/help/helpTopics'

export function expectTemplateHeader(title: string, route: string): void {
  const headings = screen.getAllByRole('heading', { level: 1 })
  expect(headings.map((h) => h.textContent)).toEqual([title])
  const header = headings[0].closest('[data-page-header]')
  expect(header, 'the h1 belongs to PageHeader, not to a page-local heading').not.toBeNull()
  expect(header).toHaveAttribute('data-compact', 'true')
  const [topic, anchor] = helpTopicParam(route).split('#')
  const help = screen.getByRole('button', { name: `Help: ${title}` })
  expect(help).toHaveAttribute('data-help-topic', topic)
  if (anchor) expect(help).toHaveAttribute('data-help-anchor', anchor)
}
