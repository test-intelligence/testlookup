/**
 * The production CSP, watched from inside the page (moved here from
 * `chart-gallery.spec.ts` for VIZ-508, whose 3D scatter spec needs the same
 * two helpers).
 *
 * The dev server serves the same index.html as production, CSP <meta> and
 * all, so a chart engine that needs `eval`, a `blob:` worker or a remote
 * resource shows up as a `securitypolicyviolation` event.
 */
import type { Page } from '@playwright/test'

/** Collect `securitypolicyviolation` events from before any page script runs. */
export async function watchCsp(page: Page) {
  await page.addInitScript(() => {
    const seen: string[] = []
    ;(window as unknown as { __cspViolations: string[] }).__cspViolations = seen
    document.addEventListener('securitypolicyviolation', (event) => {
      seen.push(`${event.violatedDirective} <- ${event.blockedURI || 'inline'}`)
    })
  })
}

/** The violations so far, or `null` when `watchCsp` was not installed on this page. */
export const cspViolations = (page: Page) =>
  page.evaluate(() => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? null)
