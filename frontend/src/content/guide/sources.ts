/**
 * Documentation content, loaded from the sibling `*.md` files.
 *
 * Separate from `DocsPage.tsx` so the page exports only its component — a
 * component file that also exports constants breaks React fast refresh, and
 * the tests need these sources without importing the page.
 *
 * Eager so a section switch is instant, and so a missing file fails at build
 * time rather than as a blank panel in front of a reader.
 */
const RAW_DOCS = import.meta.glob('./*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** manifest id -> markdown source. */
export const DOC_SOURCES: Record<string, string> = Object.fromEntries(
  Object.entries(RAW_DOCS).map(([path, body]) => [
    path.replace(/^\.\/(.+)\.md$/, '$1'),
    body,
  ]),
)
