/**
 * Renders the documentation pages that contain diagrams, so CI can measure them.
 *
 * The live probe checks the deployment, which CI cannot reach — and /docs sits
 * behind ProtectedRoute, so serving the built app here would mean faking auth
 * and coupling a documentation check to the auth implementation.
 *
 * It renders the REAL `DocsPage`, not the diagram component on its own. That
 * distinction is the whole value of this harness, and it was learned the hard
 * way: the first version mounted `<MermaidDiagram>` directly, and when the
 * original defect was reintroduced to check the harness would catch it, it
 * passed. In the app react-markdown wraps a fenced block in `<pre>`, so the
 * diagram host inherits MONOSPACE while mermaid measures its labels against the
 * body's sans font — that mismatch is what clipped every label. Mounted bare,
 * the host inherited the body font and measurement and rendering agreed, so the
 * harness could not reproduce the bug it exists to catch.
 *
 * The rule this encodes: render what the reader gets, through the same code.
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import '../../src/index.css'
import DocsPage from '../../src/pages/DocsPage'
import MermaidDiagram from '../../src/components/guide/MermaidDiagram'
import { readDiagramPalette } from '../../src/components/guide/mermaidConfig'

const SOURCES = import.meta.glob('../../src/content/guide/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Pages that contain at least one diagram, and how many each has. */
function pagesWithDiagrams(): { page: string; count: number }[] {
  const out: { page: string; count: number }[] = []
  for (const [path, body] of Object.entries(SOURCES).sort()) {
    const page = path.split('/').pop()?.replace(/\.md$/, '') ?? path
    const count = [...body.matchAll(/```mermaid\n[\s\S]*?```/g)].length
    if (count > 0) out.push({ page, count })
  }
  return out
}

// `?broken=1` adds one diagram that cannot parse.
//
// Not part of the guide — deliberately. It exists so CI can check what happens
// when a render FAILS, which is where the reader-visible defect was: mermaid's
// default is to draw its own "Syntax error in text" graphic into a scratch
// element under <body> and leave it there, so in a single-page app it shows at
// the bottom of every page for the rest of the session, including pages with no
// diagrams. Reported from /docs/troubleshooting, which has none.
const BROKEN_DIAGRAM = ['flowchart LR', '  A[Unclosed --> B['].join(String.fromCharCode(10))
const withBroken = new URLSearchParams(window.location.search).has('broken')

const pages = pagesWithDiagrams()
const expected = pages.reduce((n, p) => n + p.count, 0)

function Harness() {
  return (
    <>
      {pages.map(({ page }) => (
        // One router per page: each renders the real documentation page at its
        // own route, so every diagram sits in the DOM context the reader sees.
        <MemoryRouter key={page} initialEntries={[`/docs/${page}`]}>
          <Routes>
            <Route path="/docs/:docId" element={<DocsPage />} />
          </Routes>
        </MemoryRouter>
      ))}
      {withBroken && (
        <section data-broken="1">
          <MermaidDiagram source={BROKEN_DIAGRAM} />
        </section>
      )}
    </>
  )
}

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <Harness />
  </StrictMode>,
)

// Published for the spec: how many diagrams should appear (so a harness that
// silently rendered none cannot pass), and the palette they were drawn with (so
// a missing data-theme, which would fall back to values the app never uses, is
// caught rather than measured).
declare global {
  interface Window {
    __HARNESS__?: {
      expected: number
      pages: string[]
      palette: ReturnType<typeof readDiagramPalette>
    }
  }
}
requestAnimationFrame(() => {
  window.__HARNESS__ = {
    expected,
    pages: pages.map((p) => p.page),
    palette: readDiagramPalette(),
  }
})
