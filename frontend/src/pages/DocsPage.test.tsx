/**
 * In-app documentation.
 *
 * These tests pin four things:
 *
 *  1. the page renders, and every registered topic is reachable;
 *  2. the registry and the content files cannot drift apart;
 *  3. internal links point at topics that exist;
 *  4. the NUMBERS the documentation states match the implementation.
 *
 * (4) matters more than it looks, and (2) is what makes it durable.
 * Documentation that drifts from the code is the same defect class this
 * codebase keeps producing — a value published to a reader that nothing in the
 * system actually produces. A doc page claiming a threshold the engine no
 * longer uses is a lie with a nice font. The backend-side companion
 * (`backend/tests/regression/test_docs_match_the_engine.py`) asserts these same
 * constants from the Python side, so the pair fails if either half moves.
 *
 * Content lives in `src/content/docs/*.md`; these tests read the same sources
 * the page renders, so a claim cannot be "tested" in a file the UI never shows.
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import DocsPage from './DocsPage'
import { DOC_SOURCES } from '@/content/guide/sources'
import { DOC_PAGES, DOC_GROUPS } from '@/content/guide/manifest'

function renderDocs(path = '/docs') {
  // Real routes, not a bare render: `useParams` only populates inside a
  // matched Route, so a bare render would silently always show the default
  // topic and the deep-link test would pass without testing anything.
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/docs" element={<DocsPage />} />
        <Route path="/docs/:docId" element={<DocsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

// ── The registry and the content cannot drift ────────────────────────────────

describe('documentation registry', () => {
  it('has a content file for every registered topic', () => {
    const missing = DOC_PAGES.filter((p) => !DOC_SOURCES[p.id]?.trim())
    expect(
      missing.map((p) => p.id),
      'registered topics with no content file render as an empty panel',
    ).toEqual([])
  })

  it('has a registry entry for every content file', () => {
    const registered = new Set(DOC_PAGES.map((p) => p.id))
    const orphans = Object.keys(DOC_SOURCES).filter((id) => !registered.has(id))
    expect(
      orphans,
      'content files with no registry entry are unreachable — nothing links to them',
    ).toEqual([])
  })

  it('covers every documentation area the product needs', () => {
    // Named rather than counted: losing one should say WHICH.
    const required = [
      'introduction', 'getting-started', 'concepts', 'ingestion',
      'ai-agents', 'failure-analysis', 'flaky', 'search', 'dashboards',
      'reports', 'decisions', 'releases', 'test-management',
      'integrations', 'administration', 'architecture', 'workflows',
      'security', 'troubleshooting',
    ]
    const have = new Set(DOC_PAGES.map((p) => p.id))
    expect(required.filter((id) => !have.has(id))).toEqual([])
  })

  it('gives every topic a group the navigation renders', () => {
    for (const page of DOC_PAGES) {
      expect(DOC_GROUPS, `${page.id} has group "${page.group}"`).toContain(page.group)
    }
  })

  it('gives every topic a summary and a heading', () => {
    for (const page of DOC_PAGES) {
      expect(page.summary.length, `${page.id} needs a summary`).toBeGreaterThan(10)
      expect(DOC_SOURCES[page.id], `${page.id} must open with an H1`).toMatch(/^#\s+\S/)
    }
  })
})

// ── Links resolve ────────────────────────────────────────────────────────────

describe('internal links', () => {
  it('every /docs/... link points at a topic that exists', () => {
    const ids = new Set(DOC_PAGES.map((p) => p.id))
    const broken: string[] = []
    for (const [id, body] of Object.entries(DOC_SOURCES)) {
      for (const m of body.matchAll(/\]\(\/docs\/([a-z-]+)(#[a-z0-9-]*)?\)/g)) {
        if (!ids.has(m[1])) broken.push(`${id}.md -> /docs/${m[1]}`)
      }
    }
    expect(broken, 'a link to a topic that does not exist is a dead end').toEqual([])
  })
})

// ── Diagrams ────────────────────────────────────────────────────────────────

describe('diagrams', () => {
  it('every mermaid block is closed and non-empty', () => {
    for (const [id, body] of Object.entries(DOC_SOURCES)) {
      const opens = (body.match(/```mermaid/g) ?? []).length
      const fences = (body.match(/```/g) ?? []).length
      expect(fences % 2, `${id}.md has an unclosed code fence`).toBe(0)
      for (const m of body.matchAll(/```mermaid\n([\s\S]*?)```/g)) {
        expect(m[1].trim().length, `${id}.md has an empty mermaid block`).toBeGreaterThan(10)
      }
      expect(opens).toBeGreaterThanOrEqual(0)
    }
  })

  it('every diagram is followed by a written description', () => {
    // A picture the reader cannot render — and a screen reader never gets — is
    // not an explanation. Each diagram carries prose saying the same thing.
    const missing: string[] = []
    for (const [id, body] of Object.entries(DOC_SOURCES)) {
      for (const m of body.matchAll(/```mermaid\n[\s\S]*?```\n+([^\n]*)/g)) {
        if (!/\*\*In words:\*\*/.test(m[1])) missing.push(`${id}.md`)
      }
    }
    expect([...new Set(missing)], 'diagrams need an "In words:" description').toEqual([])
  })
})

// ── Rendering and navigation ────────────────────────────────────────────────

describe('rendering', () => {
  it('renders the default topic', () => {
    renderDocs()
    expect(screen.getByRole('heading', { name: /Documentation/i })).toBeInTheDocument()
  })

  it('lists every topic in the navigation', () => {
    renderDocs()
    for (const page of DOC_PAGES) {
      expect(
        screen.getByRole('button', { name: new RegExp(page.label, 'i') }),
      ).toBeInTheDocument()
    }
  })

  it('deep-links a topic by URL', () => {
    renderDocs('/docs/flaky')
    expect(screen.getByText(/Flakiness is about/i)).toBeInTheDocument()
  })

  it('falls back to the default topic for an unknown id', () => {
    renderDocs('/docs/not-a-real-topic')
    expect(screen.getAllByRole('heading').length).toBeGreaterThan(0)
  })

  it('filters the navigation', () => {
    renderDocs()
    fireEvent.change(screen.getByRole('searchbox', { name: /filter documentation/i }), {
      target: { value: 'flaky' },
    })
    expect(screen.getByRole('button', { name: /Flaky tests/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Administration$/i })).not.toBeInTheDocument()
  })
})

// ── The numbers match the engine ────────────────────────────────────────────

describe('stated values match the implementation', () => {
  it('states the flakiness weights the scorer actually uses', () => {
    const body = DOC_SOURCES['flaky']
    for (const weight of ['0.45', '0.25', '0.20', '0.10']) {
      expect(body, `flaky docs must state weight ${weight}`).toContain(weight)
    }
  })

  it('states the observation floor and confidence bands correctly', () => {
    const body = DOC_SOURCES['flaky']
    expect(body).toMatch(/Fewer than \*\*5\*\*/)
    expect(body).toMatch(/5 – 9/)
    expect(body).toMatch(/10 – 19/)
    expect(body).toMatch(/20 or more/)
  })

  it('does not present a missing flakiness score as zero', () => {
    const body = DOC_SOURCES['flaky']
    expect(body).toMatch(/insufficient/i)
    expect(body).toMatch(/would read as evidence of/i)
  })

  it('lists all seven risk dimensions', () => {
    const body = DOC_SOURCES['releases']
    for (const dim of [
      'User impact',
      'Environment sensitivity',
      'Reproducibility',
      'Regression likelihood',
      'Historical recurrence',
      'Blast radius',
      'Diagnosis confidence',
    ]) {
      expect(body, `release docs must list "${dim}"`).toContain(dim)
    }
  })

  it('states the decision rules in the order the engine evaluates them', () => {
    const body = DOC_SOURCES['releases']
    expect(body).toMatch(/70% of your configured bar/)
    const hardFloor = body.indexOf('70% of your configured bar')
    const otherwise = body.indexOf('Otherwise')
    expect(hardFloor).toBeGreaterThan(-1)
    expect(otherwise).toBeGreaterThan(hardFloor)
  })

  it('says bands can only tighten the verdict, never loosen it', () => {
    expect(DOC_SOURCES['releases']).toMatch(/never unblock/i)
  })

  it('tells the reader AI output is advisory, not authoritative', () => {
    expect(DOC_SOURCES['ai-agents']).toMatch(/advisory, not authoritative/i)
  })

  it('explains that a summary may be deterministic rather than model-written', () => {
    expect(DOC_SOURCES['reports']).toMatch(/fallback_used/)
  })

  it('states the analysis routing order the router actually uses', () => {
    expect(DOC_SOURCES['ai-agents']).toMatch(/ML → LLM → Rules/)
  })

  it('never claims TestLookup changes the user\'s test suite', () => {
    // Quarantine is a record, not an action. Getting this wrong would be the
    // most consequential inaccuracy in the whole guide.
    expect(DOC_SOURCES['flaky']).toMatch(/does not change your test suite/i)
  })
})

// ── The markdown actually renders ────────────────────────────────────────────

describe('markdown rendering', () => {
  it('renders GFM tables as real tables, not raw pipes', () => {
    // The defect this exists for: react-markdown does NOT support GFM tables
    // without remark-gfm. Every table in the guide — the flaky weights, the
    // confidence bands, the decision tables, the stage list — rendered as
    // pipe-delimited paragraphs on the deployment.
    //
    // The earlier tests missed it entirely because they assert on the SOURCE
    // and on innerText, and both contain the same words whether the table
    // renders or not. This asserts the DOM.
    renderDocs('/docs/flaky')

    const tables = document.querySelectorAll('article table')
    expect(tables.length, 'flaky docs contain tables that must render as tables')
      .toBeGreaterThan(0)

    const text = document.querySelector('article')?.textContent ?? ''
    expect(text, 'raw table pipes are visible — the GFM plugin is not applied')
      .not.toMatch(/\|\s*---/)
  })

  it('renders a table on every page that writes one', () => {
    const withTables = DOC_PAGES.filter((p) => /\n\|.*\|\n\|\s*-/.test(DOC_SOURCES[p.id] ?? ''))
    expect(withTables.length, 'no page has a table — this check is vacuous')
      .toBeGreaterThan(5)

    for (const page of withTables) {
      const { unmount } = renderDocs(`/docs/${page.id}`)
      expect(
        document.querySelectorAll('article table').length,
        `${page.id} writes a table that did not render`,
      ).toBeGreaterThan(0)
      unmount()
    }
  })
})
