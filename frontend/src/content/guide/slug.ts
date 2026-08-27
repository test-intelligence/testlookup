/**
 * Heading-anchor slugs for the in-app documentation.
 *
 * `DocsPage` renders every H2/H3 with `id={slugify(text)}`, and both the
 * on-page table of contents and cross-page `#fragment` links resolve against
 * those ids. This is the single source of that slug so the page, its TOC, and
 * the tests that assert every `#fragment` link lands on a real heading all
 * compute the id the same way — a fragment link and the heading it targets
 * cannot silently drift apart.
 */

/** The URL-fragment id `DocsPage` gives a heading with this text. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
}

/**
 * The anchor ids `DocsPage` actually renders for a Markdown source: the slugs
 * of its H2/H3 headings, skipping fenced code so a `##` comment inside a code
 * block is not mistaken for a heading. H1 (the page title) is not rendered with
 * an id, so it is excluded — matching what a `#fragment` link can reach.
 */
export function anchorIds(markdown: string): string[] {
  const ids: string[] = []
  let inFence = false
  for (const line of markdown.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    const m = /^(#{2,3})\s+(.*)$/.exec(line)
    if (m) ids.push(slugify(m[2].trim()))
  }
  return ids
}
