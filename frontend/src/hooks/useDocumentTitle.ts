import { useEffect } from 'react'

/**
 * The brand suffix every in-app tab title carries, and the standalone fallback
 * when a page has no title of its own. Kept short on purpose: this is the
 * per-tab label a self-hoster reads in a crowded browser window, not the
 * marketing tagline in `index.html` — a full "TestLookup — Instant Answers from
 * Your Test Results" repeated across ten open tabs is noise, not a label.
 */
export const BASE_DOCUMENT_TITLE = 'TestLookup'

/**
 * Compose the `document.title` for a page from its heading.
 *
 * Pure so the format is unit-testable without touching the DOM. A blank or
 * whitespace-only page title collapses to {@link BASE_DOCUMENT_TITLE} rather
 * than rendering a stray "· TestLookup" with nothing before the separator.
 */
export function formatDocumentTitle(pageTitle?: string): string {
  const trimmed = pageTitle?.trim()
  return trimmed ? `${trimmed} · ${BASE_DOCUMENT_TITLE}` : BASE_DOCUMENT_TITLE
}

/**
 * Set the browser-tab title for the current page.
 *
 * Every routed page in the app renders through a single static
 * `<title>TestLookup — …</title>` from `index.html`, so before this hook every
 * tab read identically: a self-hoster with Failures, the Release Gate and the
 * Docs open in three tabs could not tell them apart, and every bookmark and
 * history entry carried the same label. Driving `document.title` off the page
 * heading gives each route a distinct, shareable tab title with no per-page
 * bookkeeping to keep in sync — the heading a page already shows IS its title.
 *
 * Deliberately does not restore a previous title on unmount: during a route
 * change the outgoing page unmounts before the incoming one mounts, so a
 * restore-on-unmount would flash the base title between every navigation. The
 * next page's heading overwrites it instead; a page with no heading simply
 * keeps the last title, which is a strictly better default than the old
 * always-identical one.
 */
export function useDocumentTitle(pageTitle?: string): void {
  useEffect(() => {
    if (typeof document === 'undefined') return
    document.title = formatDocumentTitle(pageTitle)
  }, [pageTitle])
}
